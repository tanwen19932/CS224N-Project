import numpy as np
import tensorflow as tf
from tensorflow.contrib import rnn
from preprocess import readOurData
from model import Model

''' 
Set up classes and functions 
'''
class Config(object):

    def __init__(self, train_x, train_y, embeddingDict):
        self.max_sentence = train_x.shape[1]
        self.n_class = train_y.shape[1]
        self.embedding_size = embeddingDict.shape[1]
        self.drop_out = 0.5
        self.hidden_size = 200
        self.batch_size = 32
        self.epochs = 10
        self.lr = 0.001
        self.l2Reg = 1.0e-6

###############
### RNN Cell ##
###############

class RNNCell(tf.nn.rnn_cell.RNNCell):
    """Wrapper around our RNN cell implementation that allows us to play
    nicely with TensorFlow.
    """
    def __init__(self, input_size, state_size, name_suffix):
        self.input_size = input_size
        self._state_size = state_size
        self._name_suffix = name_suffix

    @property
    def state_size(self):
        return self._state_size

    @property
    def output_size(self):
        return self._state_size

    def __call__(self, inputs, state, scope=None):
        """Updates the state using the previous @state and @inputs.
        Args:
            inputs: is the input vector of size [None, self.input_size]
            state: is the previous state vector of size [None, self.state_size]
            scope: is the name of the scope to be used when defining the variables inside.
        Returns:
            a pair of the output vector and the new state vector.
        """
        scope = scope or type(self).__name__

        # It's always a good idea to scope variables in functions lest they
        # be defined elsewhere!
        with tf.variable_scope(scope):
            ## layer one 
            W_x = tf.get_variable(name = "W_x" + str(self._name_suffix),
                                  shape = (self.input_size, self._state_size),
                                  dtype = tf.float32,
                                  initializer = tf.contrib.layers.xavier_initializer())

            W_h = tf.get_variable(name = "W_h" + str(self._name_suffix),
                                  shape = (self._state_size, self._state_size),
                                  dtype = tf.float32,
                                  initializer = tf.contrib.layers.xavier_initializer())
            b = tf.get_variable(name = "b" + str(self._name_suffix),
                                shape = (self._state_size,),
                                dtype = tf.float32,
                                initializer = tf.constant_initializer(0.0))

            output = tf.tanh(tf.matmul(inputs, W_x) + tf.matmul(state, W_h) + b)
        return output

#################
### RNN Model ###
#################

class RNNModel(Model):
    def add_placeholders(self):
        # batchSize X sentence X numClasses
        self.inputPH = tf.placeholder(dtype = tf.int32,
                                 shape = (self.config.batch_size, self.config.max_sentence),
                                 name = 'input')
        # batchSize X numClasses
        self.labelsPH = tf.placeholder(dtype = tf.int32,
                                  shape = (self.config.batch_size, self.config.n_class),
                                  name = 'labels')
        # mask over sentences not long enough
        self.maskPH = tf.placeholder(dtype = tf.bool,
                                shape = (self.config.batch_size, self.config.max_sentence),
                                name = 'mask')
        self.dropoutPH = tf.placeholder(dtype = tf.float32,
                                   shape = (),
                                   name = 'dropout')
        self.seqPH = tf.placeholder(dtype = tf.float32,
                               shape = (self.config.batch_size,),
                               name = 'sequenceLen')
        self.l2RegPH = tf.placeholder(dtype = tf.float32,
                                 shape = (),
                                 name = 'l2Reg')

    def create_feed_dict(self, inputs_batch, labels_batch=None, dropout=1, mask_batch, l2_reg):
        '''
        Feed Dictionary
        '''

        feed_dict = {
            self.inputPH: inputs_batch,
            self.maskPH: mask_batch,
            self.dropoutPH: dropout,
            self.l2RegPH: l2_reg
        }

        # Add labels if not none
        if labels_batch is not None:
            feed_dict[self.labels_placeholder] = labels_batch

        return feed_dict

    def add_embedding(self):
        ''' 
        NOT YET UPDATED 
        '''

        ### YOUR CODE HERE
        pretrainEmbeds = tf.Variable(self.pretrained_embeddings)
        embeddings = tf.nn.embedding_lookup(pretrainEmbeds, self.input_placeholder)
        embeddings = tf.reshape(embeddings,[-1, self.config.n_features * self.config.embed_size])
        ### END YOUR CODE
        return embeddings

    def add_prediction_op(self):
        preds = [] # predicted output at each timestep

        cell1 = RNNCell(hidden_size, hidden_size, "cell1")
        cell2 = RNNCell(hidden_size, hidden_size, "cell2")

        # Extract sizes
        nHid = hidden_size
        nClass = n_class

        W = tf.get_variable(name = 'W',
                            shape = ((2*nHid), nClass),
                            dtype = tf.float32,
                            initializer = tf.contrib.layers.xavier_initializer())

        b =tf.get_variable(name = 'b',
                            shape = (nClass,),
                            dtype = tf.float32,
                            initializer = tf.constant_initializer(0.0))

        h1_Prev = tf.zeros(shape = (tf.shape(embedInput)[0], nHid), dtype = tf.float32)
        h2_Prev = tf.zeros(shape = (tf.shape(embedInput)[0], nHid), dtype = tf.float32)

        for time_step in range(max_sentence):
            if time_step > 0:
                tf.get_variable_scope().reuse_variables()

            # First RNN Layer - uses embeddings
            h1_t = cell1(embedInput[:, time_step, :], h1_Prev)
            h1_drop_t = tf.nn.dropout(h1_t, keep_prob = dropoutPH)

            # Second RNN Layer - uses First layer hidden states
            h2_t = cell2(h1_drop_t, h2_Prev)
            h2_drop_t = tf.nn.dropout(h2_t, keep_prob = dropoutPH)

            h1_Prev = h1_t
            h2_Prev = h2_t


        # Concatenate last states of first and second layer for prediction layer
        h_t = tf.concat(concat_dim = 1, values = [h1_drop_t, h2_drop_t])
        y_t = tf.tanh(tf.matmul(h_t, W) + b)
        preds.append(y_t)
        return pred

    def add_loss_op(self, pred):
        # Compute L2 loss
        loss = tf.nn.l2_loss(tf.cast(labelsPH, dtype=tf.float32) - preds)
        loss = tf.reduce_mean(loss)

        # # Apply L2 regularization
        regularization = tf.reduce_sum([tf.nn.l2_loss(v) for v in tf.trainable_variables()])

        totalCost = (10.0 * loss) + (l2RegPH * regularization)
        return totalCost

    def add_training_op(self, totalCost):
        opt = tf.train.AdamOptimizer(learning_rate = lr)
        train_op = opt.minimize(totalCost)
        return train_op

    ## TODO: Add def evaluate(test_set)

    ### NO NEED TO UPDATE BELOW 
    def train_on_batch(self, sess, inputs_batch, labels_batch):
        feed = self.create_feed_dict(inputs_batch, labels_batch=labels_batch,
                                     dropout=self.config.dropout)
        _, loss = sess.run([self.train_op, self.loss], feed_dict=feed)
        return loss

    def run_epoch(self, sess, parser, train_examples, dev_set):
        prog = Progbar(target=1 + len(train_examples) / self.config.batch_size)
        for i, (train_x, train_y) in enumerate(minibatches(train_examples, self.config.batch_size)):
            loss = self.train_on_batch(sess, train_x, train_y)
            prog.update(i + 1, [("train loss", loss)])

        print "Evaluating on dev set",
        dev_UAS, _ = parser.parse(dev_set)
        print "- dev UAS: {:.2f}".format(dev_UAS * 100.0)
        return dev_UAS

    def fit(self, sess, saver, parser, train_examples, dev_set):
        best_dev_UAS = 0
        for epoch in range(self.config.n_epochs):
            print "Epoch {:} out of {:}".format(epoch + 1, self.config.n_epochs)
            dev_UAS = self.run_epoch(sess, parser, train_examples, dev_set)
            if dev_UAS > best_dev_UAS:
                best_dev_UAS = dev_UAS
                if saver:
                    print "New best dev UAS! Saving model in ./data/weights/parser.weights"
                    saver.save(sess, './data/weights/parser.weights')
            print

    ## add def eval here

    def __init__(self, config, pretrained_embeddings):
        self.pretrained_embeddings = pretrained_embeddings
        self.config = config
        self.build()


''' 
Creates Batch Data
'''

def data_iterator(data, labels, batch_size, sentLen):
    """ A simple data iterator """
    numObs = data.shape[0]
    while True:
        # shuffle labels and features
        idxs = np.arange(0, numObs)
        np.random.shuffle(idxs)
        shuffledData = data[idxs]
        shuffledLabels = labels[idxs]
        shuffledSentLen = sentLen[idxs]
        for idx in range(0, numObs, batch_size):
            dataBatch = shuffledData[idx:idx + batch_size]
            labelsBatch = shuffledLabels[idx:idx + batch_size]
            seqLenBatch = shuffledSentLen[idx:idx + batch_size]
            yield dataBatch, labelsBatch, seqLenBatch
            

'''
Read in Data
'''

train = '/Users/henryneeb/CS224N-Project/source/rcnn-master/beer/reviews.aspect1.small.train.txt.gz'
dev = '/Users/henryneeb/CS224N-Project/source/rcnn-master/beer/reviews.aspect1.small.heldout.txt.gz'
embedding = '/Users/henryneeb/CS224N-Project/source/rcnn-master/beer/review+wiki.filtered.200.txt.gz'

train_x_pad, train_y, train_mask, dev_x_pad, dev_y, dev_mask,embeddingDictPad = readOurData(train, dev, embedding)

'''
Get Embeddings
'''
embeddings = tf.constant(embeddingDictPad, dtype = tf.float32)
embedInput = tf.nn.embedding_lookup(embeddings, inputPH)
embedInput = tf.reshape(embedInput,
                        shape = (batch_size, max_sentence, embedding_size))
# revEmbedInput = tf.reverse(embedInput, dims = [False, True, False])
# embedInput = tf.unstack(embedInput, axis = 1)
revEmbedInput = embedInput[::-1]

'''
Batch our Data
'''
iter = data_iterator(train_x_pad, train_y, batch_size, sentLen)


##################
# GENERATOR STEP #
##################

'''
TODO: Fill this in. 
'''
# output2Rev = tf.reverse(output2, axis = 1)
# hFinal = tf.concat(concat_dim = 0, values = [output1, output2Rev])


def main(debug=False):
    print 80 * "="
    print "INITIALIZING"
    print 80 * "="
    config = Config()
    ## this is where we add our own data 
    parser, embeddings, train_examples, dev_set, test_set = load_and_preprocess_data(debug)
    if not os.path.exists('./data/weights/'):
        os.makedirs('./data/weights/')

    with tf.Graph().as_default():
        print "Building model...",
        start = time.time()
        ## this is where we add our model class name 
        ## config is also a class name
        model = RNNModel(config, embeddings)
        rnn.model = model
        print "took {:.2f} seconds\n".format(time.time() - start)

        init = tf.initialize_all_variables()
        # If you are using an old version of TensorFlow, you may have to use
        # this initializer instead.
        # init = tf.initialize_all_variables()
        saver = None if debug else tf.train.Saver()

        with tf.Session() as session:
            rnn.session = session
            session.run(init)

            print 80 * "="
            print "TRAINING"
            print 80 * "="
            ## this is a function is the model class 
            model.fit(session, saver, parser, train_examples, dev_set)

            if not debug:
                print 80 * "="
                print "TESTING"
                print 80 * "="
                print "Restoring the best model weights found on the dev set"
                saver.restore(session, './data/weights/parser.weights')
                print "Final evaluation on test set",
                ## we won't have this. we need function in our model that will evaluate on test set
                ## this is a function that will only calculate loss, "Evaluate function" takes inputs and compares to labels
                ## ie model.evaluate(test_set)
                loss = model.evaluate(test_set)
                print "- test UAS: {:.2f}".format(UAS * 100.0)
                print "Writing predictions"
                with open('q2_test.predicted.pkl', 'w') as f:
                    cPickle.dump(dependencies, f, -1)
                print "Done!"

if __name__ == '__main__':
    main()
